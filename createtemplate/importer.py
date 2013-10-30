import os
import subprocess
import shutil
# cElementTree is C implementation and faster
# http://eli.thegreenplace.net/2012/03/15/processing-xml-in-python-with-elementtree/
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from trac.core import *
from trac.wiki.model import WikiPage
from trac.ticket.model import Type, Milestone

from logicaordertracker.controller import LogicaOrderController
from simplifiedpermissionsadminplugin.simplifiedpermissions import SimplifiedPermissions
from mailinglistplugin.model import Mailinglist

# Author: Danny Milsom <danny.milsom@cgi.com>

class ImportTemplate(Component):
    """Creates data and components inside #define based on XML template files"""

    def import_wiki_pages(self, template_name):
        """Creates wiki pages inside the project using data extracted from
        an XML file."""

        # open the XML file and parse the data
        self.log.info("Creating wiki pages from template")
        full_path = os.path.join(os.getcwd(), 'templates', template_name, 'wiki.xml')
        tree = ET.ElementTree(file=full_path)
        # iterate through the tree and get info
        for page in tree.getroot():
            properties = page.getchildren()[0]
            attrib = properties.attrib

            # we dont care about the author or version
            wikipage = WikiPage(self.env, page.attrib['name'])
            wikipage.readonly = int(attrib['readonly'])
            wikipage.text = properties.getchildren()[0].text
            wikipage.save(None, None, None)
            self.log.info("Wiki page %s created" % page.attrib['name'])

    def import_wiki_attachments(self, template_name, project_name):
        """Imports wiki files and inserts data into the attachment table"""

        # copy the actual files
        self.log.info("Importing attachment files from template directory")
        template_attachment_path = os.path.join(os.getcwd(), 'templates', template_name, 'attachments', 'wiki')
        project_attachment_path = os.path.join(os.getcwd(), 'projects', project_name, 'attachments', 'wiki')
        # the directory we copy to can't exist before this
        if os.path.exists(project_attachment_path):
            shutil.rmtree(project_attachment_path)
        shutil.copytree(template_attachment_path, project_attachment_path)
        self.log.info("Copied wiki attachments to %s", project_attachment_path)

        @self.env.with_transaction()
        def clear_and_insert_attachments(db):
            """Clears any wiki attachments from the current attachment table
            and inserts new rows based on attachment info from xml templates"""

            cursor = db.cursor()
            cursor.execute("DELETE FROM attachment WHERE type='wiki'")

            filepath = os.path.join(os.getcwd(), 'templates', template_name, 'attachment.xml')
            tree = ET.ElementTree(file=filepath)
            attachment_info = [('wiki', att.attrib['parent_id'], att.attrib['name'], 
                                att.attrib['size'], att.text)
                                for att in tree.getroot()]

            cursor.executemany("""INSERT INTO attachment(type, id, filename, size, description)
                                  VALUES (%s, %s, %s, %s, %s)""", attachment_info)

    def template_populate(self, template_name, project_name):
        """Clears tables of define default data and repopulates them with 
        template data taken from XML files.

        This function takes inspiration from define/env.py and the
        _clean_populate() method - although it allows us to only 
        delete certain records, not only full tables."""

        # for vales stored in the enum table we only want to clear certain rows
        enum_to_clear = list()

        # go through template dir to see which tables and rows we want to modify
        dir_path = os.path.join(os.getcwd(), 'templates', template_name)
        for filename in os.listdir(dir_path):
            if filename.lower().endswith("permission.xml"):
                self.import_perms(template_name)
            elif filename.lower().endswith("group.xml"):
                self.import_groups(template_name)
            elif filename.lower().endswith("milestone.xml"):
                self.import_milestones(template_name)
            elif filename.lower().endswith("priority.xml"):
                enum_to_clear.append("priority")
            elif filename.lower().endswith("ticket.xml"):
                enum_to_clear.append("ticket_type")

        if enum_to_clear:
            self.import_enum(template_name, enum_to_clear, project_name)

    def import_perms(self, template_name):
        """Parses the permissions XML file to get the data we need to insert
        into the permissions table. If we have this data we clear the existing
        permission data, and then insert the template data with an executemany()
        cursor method.

        If we don't create a perm_data list, we exit the function and 
        continue to use default data."""

        # parse the tree to get username, action data
        dir_path = os.path.join(os.getcwd(), 'templates', template_name)
        for xml_file in os.listdir(dir_path):
            if xml_file.endswith("permission.xml"):
                path = os.path.join(os.getcwd(), 'templates', template_name, "permission.xml")
                tree = ET.ElementTree(file=path)
                perm_data = [(perm.attrib['name'], perm.attrib['action']) for perm in tree.getroot()]
            else:
                self.log.info("No permission data so using default data")
                return

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

    def import_groups(self, template_name):
        """Create project groups from templates after clearing the existing
        data in the groups table."""

        @self.env.with_transaction()
        def clear_perms(db):
            """Clears the whole groups table of default data. You can't pass
            a table name as an argument for parameter substitution, so it
            has to be hard coded."""
            cursor = db.cursor()
            self.log.info("Clearing permissions table")
            cursor.execute("DELETE FROM groups")

        self.log.info("Creating groups from template")
        path = os.path.join(os.getcwd(), 'templates', template_name, "group.xml")
        tree = ET.ElementTree(file=path)
        for group in tree.getroot():
            SimplifiedPermissions(self.env).add_group(group.attrib['name'], description=group.text)

    def import_milestones(self, template_name):
        """Deletes existing trac default milestones and creates new ones
        based on the information in milestone XML template"""

        @self.env.with_transaction()
        def clear_milestones(db):
            """Clears all rows in milestone table. This value is hard coded
            as you can't pass a table name with parameter substitution."""

            cursor = db.cursor()
            cursor.execute("""DELETE FROM milestone""")

        # Parse the XML tree and create milestones
        path = os.path.join(os.getcwd(), 'templates', template_name, "milestone.xml")
        tree = ET.ElementTree(file=path)
        for m in tree.getroot():
            milestone = Milestone(self.env)
            if 'name' in m.attrib:
                milestone.name = m.attrib['name']
            if 'start' in m.attrib:
                milestone.start = m.attrib['start']
            if 'due' in m.attrib:
                milestone.due = m.attrib['due']
            if 'completed' in m.attrib:
                milestone.completed = m.attrib['completed']
            if m.text:
                milestone.description = m.text
            # what if the parent doesnt exist
            #if m.parent:
                #milestone.parent = m.attrib['parent']
            # save the milestone
            milestone.insert()

    def import_enum(self, template_name, types_to_remove, project_name):
        """Removes types from the enum table and then inserts data from the 
        template XML files."""

        # create a list of tuples for every enum type in our template 
        # where the tuple follows the synax (type, name, value)
        template_path = os.path.join(os.getcwd(), 'templates', template_name)
        for xml_file in os.listdir(template_path):
            if xml_file.endswith("priority.xml"):
                path = os.path.join(os.getcwd(), 'templates', template_name, 'priority.xml')
                tree = ET.ElementTree(file=path)
                priority_list = [('priority', priority.attrib['name'], priority.attrib['value']) for priority in tree.getroot()]

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

        # now import ticket types from template
        # we use LogicaOrderController rather than a straight SQL insert
        self.import_ticket_types(template_path) 
        self.import_workflows(template_path, project_name)

    def import_ticket_types(self, template_path):
        """Create ticket types using the import functionality from 
        LogicaOrderController and data from a ticket type template 
        XML."""

        # get ticket info in JSON format from XML file
        controller = LogicaOrderController(self.env)

        self.log.info("Creating ticket types from template")
        tree = ET.ElementTree(file=os.path.join(template_path, 'ticket.xml'))
        for ticket in tree.getroot():
            # using a _method() is a bit naughty
            controller._import_ticket_type(ticket.text, dry_run=False)

    def import_workflows(self, template_name, project_name):
        # copy the template files
        self.log.info("Importing project specific workflows into template directory")
        template_workflow_path = os.path.join(os.getcwd(), 'templates', template_name, 'workflows')
        project_workflow_path = os.path.join(os.getcwd(), 'projects', project_name, 'workflows')

        # the directory we copy to can't exist if using shutil.copytree
        # but it is created in manage_project()
        if os.path.exists(project_workflow_path):
            shutil.rmtree(project_workflow_path)
        shutil.copytree(template_workflow_path, project_workflow_path)
        self.log.info("Copied ticket workflows to %s", project_workflow_path)

    def import_mailinglist(self, template_name):
        """Create new mailing lists based on the mailng list XML 
        template."""

        path = os.path.join(os.getcwd(), 'templates', template_name, 'list.xml')
        tree = ET.ElementTree(file=path)
        for ml in tree.getroot():
            mailinglist = Mailinglist(self.env, emailaddress=ml.attrib['email'],
                                           name=ml.attrib['name'],
                                           description=ml.text,
                                           private=ml.attrib['private'],
                                           postperm=ml.attrib['postperm'],
                                           replyto=ml.attrib['replyto'])
            mailinglist.insert()

        # TODO Get Subscriber informaiton 
        # mailinglist.subscribe(group='project_group', poser=True)

    def import_file_archive(self, template_name):
        """Create a new subversion repository using the dump file in 
        the template directory."""

        path_to_dump = os.path.join(os.getcwd(), 'templates', template_name,  template_name + '.dump.gz')
        subprocess.call("zcat %s | svnadmin load newrepo" % path_to_dump, cwd=os.getcwd(), shell=True)
        self.log.info("Created new Subversion file archive")