import os
import subprocess
import shutil
import errno
# cElementTree is C implementation and faster
# http://eli.thegreenplace.net/2012/03/15/processing-xml-in-python-with-elementtree/
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from trac.core import *
from trac.wiki.model import WikiPage
from trac.ticket import model
from trac.config import PathOption
from trac.util.datefmt import parse_date

from logicaordertracker.controller import LogicaOrderController
from simplifiedpermissionsadminplugin.simplifiedpermissions import SimplifiedPermissions
from mailinglistplugin.model import Mailinglist

# Author: Danny Milsom <danny.milsom@cgi.com>

class ImportTemplate(Component):
    """Creates data and components inside #define based on XML template files"""

    template_dir_path = PathOption('project_templates', 'template_dir', 
                        doc="The default path for the project template directory")

    def import_wiki_pages(self, template_path):
        """Creates wiki pages from wiki.xml template file.

        Creates wiki pages inside the project using data extracted from
        an wiki.ml file. We don't set the author or version as that wouldn't 
        be applicable to a new project.
        """

        # open the wiki XML file, parse the data and create wiki pages
        full_path = os.path.join(template_path, 'wiki.xml')
        try:
            tree = ET.ElementTree(file=full_path)
            for page in tree.getroot():
                if page.text:
                    wikipage = WikiPage(self.env, page.attrib['name'])
                    wikipage.readonly = int(page.attrib['readonly']) # we store as a string in xml
                    wikipage.text = page.text
                    wikipage.save(None, None, None)
                    self.log.info("Wiki page %s created", page.attrib['name'])
                else:
                    self.log.debug("Cannot create wiki pages with no text. "
                                   "Unable to import %s", wikipage)
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to wiki.xml file %s does not exist. Unable "
                              "to import wiki pages from template.", full_path)

    def import_wiki_attachments(self, template_path):
        """Imports wiki attachments from template.

        Imports wiki attachment files and inserts associated data into 
        the attachment wiki table.
        """

        # check that there are attachments to import
        template_attachment_path = os.path.join(template_path, 'attachments', 'wiki')
        if os.path.isdir(template_attachment_path):

            # the directory we copy to can't exist before the copytree() so we delete it just incase
            project_attachment_path = os.path.join(self.env.path, 'attachments', 'wiki')
            try:
                shutil.rmtree(project_attachment_path)
            except OSError as exception:
                if exception.errno == errno.ENOENT:
                    self.log.debug("No directory at %s to delete", project_attachment_path)

            # copy the actual files (and create the attachment dir)
            try:
                shutil.copytree(template_attachment_path, project_attachment_path)
                self.log.info("Copied wiki attachments to %s", project_attachment_path)
            except OSError as exception:
                # incase the path doesn't exist
                if exception.errno == errno.ENOENT:
                    self.log.info("The path to the template attachment directory "
                                  "at %s does not exist", template_attachment_path)
                    return

            # insert meta-data into the wiki attachment table
            @self.env.with_transaction()
            def clear_and_insert_attachments(db):
                """Clears any wiki attachments from the current attachment table
                and inserts new rows based on attachment info from xml templates"""

                cursor = db.cursor()
                cursor.execute("DELETE FROM attachment WHERE type='wiki'")

                filepath = os.path.join(template_path, 'attachment.xml')
                tree = ET.ElementTree(file=filepath)
                attachment_info = [('wiki', att.attrib['parent_id'], att.attrib['name'], 
                                    att.attrib['version'], att.attrib['size'], att.text)
                                    for att in tree.getroot()]

                cursor.executemany("""INSERT INTO attachment(type, id, filename, version, size, description)
                                      VALUES (%s, %s, %s, %s, %s, %s)""", attachment_info)

    def template_populate(self, template_path):
        """Clears default data and inserts template specific data from xml files.

        Clears tables of define/trac default data and repopulates them with 
        template data taken from various XML files.

        This function takes inspiration from define/env.py and the
        _clean_populate() method - although it allows us to only 
        delete certain records, not only full tables.

        First we deal with seperate tables such as the milestone, group
        and version tables - then we move onto the enum table.
        """


        importer_functions = {'permission.xml': self.import_perms,
                             'group.xml': self.import_groups,
                             'milestone.xml': self.import_milestones,
                             'component.xml': self.import_components,
        }

        enum_values = {'priority.xml': 'priority',
                       'ticket.xml': 'ticket_type',
        }

        # for vales stored in the enum table we only want to clear certain rows
        enum_to_clear = list()

        # go through template dir to see which tables and rows we want to modify
        try:
            for filename in os.listdir(template_path):
                if filename in importer_functions:
                    importer_functions[filename](template_path)

                elif filename in enum_values:
                    enum_to_clear.append(enum_values[filename])

        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Unable to list files at %s."
                              "Import of template data failed.", template_path)

        if enum_to_clear:
            self.import_enum(template_path, enum_to_clear)

    def import_perms(self, template_path):
        """Creates permissions from data stored in groups.xml.

        Parses this XML file to get the data we need to insert into the 
        permissions table. If we have this data we clear the existing
        permission data, and then insert the template data with an 
        executemany() cursor method.

        If we don't create a perm_data list, we exit the function and 
        continue to use default data.
        """

        # parse the tree to get username, action data
        path = os.path.join(template_path, "groups.xml")
        try:
            tree = ET.ElementTree(file=path)
            perm_data = [(subelement.attrib['name'], subelement.attrib['action']) 
                         for perm in tree.getroot() for subelement in perm
                         if subelement.attrib['name'].strip()]
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to groups.xml at %s does not exist. "
                              "Unable to import permissions", path)

        @self.env.with_transaction()
        def clear_and_insert_perms(db):
            """Clears the whole permissions table of default data, 
            and then inserts data from template."""

            cursor = db.cursor()
            self.log.info("Clearing permissions table")
            # cant pass the table name as an arg so its hard coded
            cursor.execute("DELETE FROM permission")

            self.log.info("Inserting template data into permissions table")
            cursor.executemany("""INSERT INTO permission(username, action)
                                  VALUES (%s, %s)""", perm_data)

    def import_groups(self, template_path):
        """Create project groups from group.xml template file.

        First we clear the existing data in the groups table and then we insert
        group data taken from the group.xml file."""

        @self.env.with_transaction()
        def clear_groups(db):
            """Clears the whole groups table of default data. You can't pass
            a table name as an argument for parameter substitution, so it
            has to be hard coded."""
            cursor = db.cursor()
            self.log.info("Clearing permissions table")
            cursor.execute("DELETE FROM groups")

        self.log.info("Creating groups from template")
        path = os.path.join(template_path, "group.xml")
        try:
            tree = ET.ElementTree(file=path)
            for group in tree.getroot():
                # have to use _new_group() not add_group() otherwise we can't specify the sid
                if 'sid' in group.attrib:
                    SimplifiedPermissions(self.env)._new_group(group.attrib['sid'], 
                                group.attrib['name'], description=group.text)
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to group.xml at %s does not exist. Unable to "
                              "import group data from template.", path)

    def import_milestones(self, template_path):
        """Create project milestones from milestone.xml template file.

        Deletes existing trac default milestones and creates new ones
        based on the information in milestone XML template.
        """

        @self.env.with_transaction()
        def clear_milestones(db):
            """Clears all rows in milestone table. This value is hard coded
            as you can't pass a table name with parameter substitution."""

            cursor = db.cursor()
            cursor.execute("""DELETE FROM milestone""")

        # Parse the XML tree and create milestones
        path = os.path.join(template_path, "milestone.xml")
        try:
            tree = ET.ElementTree(file=path)
            for m in tree.getroot():
                milestone = model.Milestone(self.env)
                if 'name' in m.attrib:
                    milestone.name = m.attrib['name']
                if 'start' in m.attrib:
                    milestone.start = parse_date(m.attrib['start'])
                if 'due' in m.attrib:
                    milestone.due = parse_date(m.attrib['due'])
                if 'completed' in m.attrib:
                    milestone.completed = parse_date(m.attrib['completed'])
                if 'parent' in m.attrib:
                    milestone.parent = m.attrib['parent']
                if m.text:
                    milestone.description = m.text
                # save the milestone
                milestone.insert()
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to milestone.xml at %s does not exist. "
                              "Unable to import milestone data from tempalte.", path)

    def import_versions(self, template_path):
        """Create project milestones from milestone.xml template file.

        Create ticket verions from template after clearing the existing
        data in the version table.
        """

        @self.env.with_transaction()
        def clear_versions(db):
            """Clears the whole version table of default data. You can't pass
            a table name as an argument for parameter substitution, so it
            has to be hard coded."""
            cursor = db.cursor()
            self.log.info("Clearing version table")
            cursor.execute("DELETE FROM version")

        self.log.info("Creating versions from template")
        path = os.path.join(template_path, "version.xml")
        try:
            tree = ET.ElementTree(file=path)
            for version in tree.getroot():
                ver = model.Version(self.env)
                ver.name = version.attrib['name']
                ver.description = version.attrib['description']
                ver.insert()
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to version.xml at %s does not exist. Unable to "
                              "import version data from template.", path)

    def import_components(self, template_path):
        """Create project components from component.xml template file.

        Create project component fields from template after clearing the 
        existing default data in the component table.
        """

        @self.env.with_transaction()
        def clear_components(db):
            """Clears the whole component table of default data. You can't pass
            a table name as an argument for parameter substitution, so it
            has to be hard coded."""
            cursor = db.cursor()
            self.log.info("Clearing component table")
            cursor.execute("DELETE FROM component")

        self.log.info("Creating components from template")
        path = os.path.join(template_path, "component.xml")
        try:
            tree = ET.ElementTree(file=path)
            for component in tree.getroot():
                # not exporting owner as they might not be a member
                # of the new project who use this template
                comp = model.Component(self.env)
                comp.name = component.attrib['name']
                comp.description = component.attrib['description']
                comp.insert()
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to component.xml at %s does not exist. Unable to "
                              "import component data from template.", path)

    def import_enum(self, template_path, types_to_remove):
        """Removes types from the enum table and then inserts data from the 
        template XML files."""

        # create a list of tuples for every enum type in our template 
        # where the tuple follows the synax (type, name, value)
        path = os.path.join(template_path, 'priority.xml')
        try:
            tree = ET.ElementTree(file=path)
            priority_list = [('priority', priority.attrib['name'], priority.attrib['value']) for priority in tree.getroot()]
        except IOError:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to priority.xml at %s does not exist", path)
                # return before we clear the enum table
                return
        values = list()
        values.extend(priority_list)

        @self.env.with_transaction()
        def clear_and_insert_enum(db):
            """Clears enum table rows where data type is replicated in our 
            template, such as priorty and timeaccount."""

            cursor = db.cursor()
            self.log.info("Clearing enum table")
            cursor.execute("""DELETE FROM enum
                              WHERE type IN ({0})
                              """.format(','.join(('%s',)*len(types_to_remove))),
                              types_to_remove)

            self.log.info("Inserting template data into enum table")
            cursor.executemany("""INSERT INTO enum (type, name, value) 
                                  VALUES (%s, %s, %s)
                                  """, values)

        # now import ticket types and associated data from template
        # we use LogicaOrderController rather than a straight SQL insert
        # we must import workflows first else importing types 
        # which rely on these workflows fails
        self.import_workflows(template_path)
        self.import_ticket_types(template_path) 

    def import_ticket_types(self, template_path):
        """Imports ticket types from ticket.xml template file.

        Create ticket types using the import functionality from 
        LogicaOrderController and data from a ticket type template XML.
        """

        # get ticket info in JSON format from XML file
        controller = LogicaOrderController(self.env)

        self.log.info("Creating ticket types from template")
        path = os.path.join(template_path, 'ticket.xml')
        try:
            tree = ET.ElementTree(file=path)
            for ticket in tree.getroot():
                # using a _method() is a bit naughty
                controller._import_ticket_type(ticket.text, dry_run=False)
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to ticket.xml at %s does not exist. "
                              "Unable to import tickets from tempalte.", path)

    def import_workflows(self, template_path):
        """Imports workflows from template workflow directory.

        Copies all workflow files from the template directory to our new 
        project's workflow directory.
        """

        template_workflow_path = os.path.join(template_path, 'workflows')
        project_workflow_path = os.path.join(self.env.path, 'workflows')

        # the directory we copy to can't exist if using shutil.copytree
        # but it is created in manage_project()
        try:
            shutil.rmtree(project_workflow_path)
        except OSError:
            self.log.debug("No workflow directory at %s to remove", project_workflow_path)

        try:
            shutil.copytree(template_workflow_path, project_workflow_path)
            self.log.info("Copied ticket workflows to %s", project_workflow_path)
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("The path to the workflow directory at %s does "
                              "not exist. Unable to import workflows.", template_workflow_path)

    def import_mailinglist(self, template_path):
        """Creates project mailing lists from mailinglist.xml template file."""

        path = os.path.join(template_path, 'mailinglist.xml')
        try:
            tree = ET.ElementTree(file=path)
            for ml in tree.getroot():
                mailinglist = Mailinglist(self.env, emailaddress=ml.attrib['email'],
                                               name=ml.attrib['name'],
                                               description=ml.text,
                                               private=ml.attrib['private'],
                                               postperm=ml.attrib['postperm'],
                                               replyto=ml.attrib['replyto'])
                mailinglist.insert()
        except IOError as exception:
            if exception.errno == errno.ENOENT:
                self.log.info("Path to mailinglist.xml at %s does not exist. "
                              "Unable to import mailing lists from template.", path)

        # TODO Get Subscriber informaiton 
        # mailinglist.subscribe(group='project_group', poser=True)

    def import_file_archive(self, template_path):
        """Import the file archive from template directory.
        
        Create a new subversion repository using the dump file in 
        the template directory. We don't support Git right now.
        """

        template_name = os.path.basename(os.path.normpath(template_path))
        old_repo_path = os.path.join(template_path,  template_name + '.dump.gz')

        # should probably use ResourceManager from trac/versioncontrol...
        new_repo_path = self.env.config.get('trac', 'repository_dir')

        subprocess.call("zcat %s | svnadmin load %s" % (old_repo_path, new_repo_path), cwd=os.getcwd(), shell=True)
        self.log.info("Imported Subversion file archive from %s" % old_repo_path)