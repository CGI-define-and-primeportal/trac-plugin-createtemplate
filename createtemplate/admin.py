import pkg_resources
import os
import datetime
import shutil
import subprocess
import errno
# cElementTree is C implementation and faster
# http://eli.thegreenplace.net/2012/03/15/processing-xml-in-python-with-elementtree/
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from trac.core import *
from trac.web.chrome import ITemplateProvider, add_script, add_notice
from trac.admin.api import IAdminPanelProvider
from trac.wiki.model import WikiPage
from trac.wiki.api import WikiSystem
from trac.ticket.model import Type, Milestone
from logicaordertracker.controller import LogicaOrderController
from trac.perm import DefaultPermissionStore
from trac.ticket import Priority
from trac.attachment import Attachment
from trac.config import PathOption

from simplifiedpermissionsadminplugin.model import Group
from mailinglistplugin.model import Mailinglist

# Author: Danny Milsom <danny.milsom@cgi.com>

class GenerateTemplate(Component):
    """Generates files which can be used by other projects as a base template"""

    template_dir_path = PathOption('project_templates', 'template_dir', '/var/define/templates',
                    doc="The default path for the project template directory")

    implements(IAdminPanelProvider, ITemplateProvider)

    # IAdminPanelProvider

    def get_admin_panels(self, req):
        if 'LOGIN_ADMIN' in req.perm:
            yield ('general', ('General'),
           'create_template', ('Create Template'))

    def render_admin_panel(self, req, category, page, path_info):
        if page == 'create_template':
            if req.method == 'POST':
                # server side check that there is a template name
                # there is jquery validation server side too
                if not req.args['template-name']:
                    add_notice(req, "Please enter a template name")
                    return 'template_admin.html', {}

                # create a directory to hold templates if there isn't already one
                try:
                    os.mkdir(self.template_dir_path)
                    self.log.debug("Created template directory at %s", self.template_dir_path)
                except OSError as exception:
                    if exception.errno == errno.EEXIST:
                        self.log.debug("Template directory already exists at %s", self.template_dir_path)

                # if there is already a template with the same name we prompt user for an alternative
                # we can catch this on client side when he have a way to
                # get all templates on different servers
                template_name = req.args['template-name']
                template_path = os.path.join(self.template_dir_path, template_name)

                try:
                    os.mkdir(template_path)
                    self.log.debug("Created directory for project template at", template_path)
                except OSError as exception:
                    if exception.errno == errno.EEXIST:
                        data = {'failure':True,
                                'template_name':template_name}
                        return 'template_admin.html', data

                # so far so good - now what data should we export
                if 'wiki' in req.args:
                    self.export_wiki_pages(template_path)
                    self.export_wiki_attachments(req, template_name)
                if 'ticket' in req.args:
                    self.export_ticket_types(template_path)
                    self.export_workflows(req, template_path)
                    # we export permissions if we export tickets, else
                    # the values availble in the priority field could be different
                    self.export_priorites(template_path)
                if 'archive' in req.args:
                    self.export_file_archive(req, os.path.join(template_path, template_name + '.dump.gz'))
                if 'group' in req.args:
                    self.export_groups(template_path)
                    # we export permissions only if groups are selected, 
                    # otherwise the permissions table might refer to groups
                    # which don't exist in the project
                    self.export_permissions(template_path)
                if 'list' in req.args:
                    self.export_lists(template_path)
                if 'milestone' in req.args:
                    self.export_milestones(template_path)

                # create an info file to store the exact time of template
                # creation, username of template creator etc.
                self.create_template_info_file(req, template_path)

                add_script(req, 'createtemplate/js/create_template_admin.js')
                data = {'success':True}
                return 'template_admin.html', data
            else:
                add_script(req, 'createtemplate/js/create_template_admin.js')
                return 'template_admin.html', {}

    def export_wiki_pages(self, template_path):
        """Get data for each wiki page that has not been deleted and place
        that inside an XML new tree. When we've finished building the tree, 
        create a new XML file to store the content."""
        # Get page names and create wiki page objects
        # get_pages() already excludes deleted pages
        wiki_names = WikiSystem(self.env).get_pages()
        if wiki_names:
            project_wiki = [WikiPage(self.env, wiki_page) for wiki_page in wiki_names]

            # create an XML tree using ElementTree
            template_date = datetime.date.today().strftime("%Y-%m-%d")
            root = ET.Element("wiki", project=self.env.project_name, date=template_date)
            for wiki in project_wiki:
                page = ET.SubElement(root, "page", name=wiki.name, 
                                                   readonly=str(wiki.readonly),
                                                   author=str(wiki.author)
                                    ).text = wiki.text

            # create the actual xml file
            filename = os.path.join(template_path, 'wiki.xml')
            ET.ElementTree(root).write(filename)
            self.log.info("File %s has been created at %s" % (filename, template_path))

    def export_wiki_attachments(self, req, template_name):
        """Exports files attached to wiki pages. To do this we need
        to export the wiki attachment data and put that into an XML file, 
        plus we need to store the actual files in our template directory!"""

        # Get information about attachments
        # Not really a nice way to get all the attachments in trac/attachments
        attachments = list()
        for wiki_name in WikiSystem(self.env).get_pages():
            for attachment in Attachment.select(self.env, 'wiki', wiki_name):
                if attachment.exists:
                    attachments.append(attachment)

        # write this information to XML tree if there are attachments to export
        if attachments:
            template_date = datetime.date.today().strftime("%Y-%m-%d")
            self.log.info("Creating wiki attachment XML file for template archive")
            root = ET.Element("attachments", project=self.env.project_name, date=template_date)
            for attachment in attachments:
                ET.SubElement(root, "attachment", name=attachment.filename, 
                                                  parent_id=attachment.parent_id,
                                                  size=str(attachment.size),
                                                  version=str(attachment.version)).text = attachment.description

            # create the xml file
            filename = os.path.join(self.template_dir_path, template_name, "attachment.xml")
            ET.ElementTree(root).write(filename)
            self.log.info("File %s has been created at %s" % (filename, os.path.join(self.template_dir_path, template_name)))

            # copy the project attachments into our new directory
            attachment_dir_path = os.path.join(self.env.path, 'attachments', 'wiki')
            attachment_template_path = os.path.join(self.template_dir_path, template_name, 'attachments', 'wiki')

            # the directory we copy to can't exist before shutil.copytree()
            try:
                shutil.rmtree(attachment_template_path)
            except OSError as exception:
                # no directory to remove
                if exception.errno == errno.ENOENT:
                    self.log.debug("No workflow directory at %s to remove", attachment_template_path)

            # now copy the directory
            shutil.copytree(attachment_dir_path, attachment_template_path)
            self.log.info("Copied wiki attachments to %s", attachment_template_path)

    def export_ticket_types(self, template_path):
        """Creates a dictionary where each key is a ticket type and the value
        is ticket type information. We then iterate over this to create a XML
        file using ElementTree lib"""

        types = [ticket_type.name for ticket_type in Type.select(self.env)]

        ticket_types_dict = dict()
        controller = LogicaOrderController(self.env)
        for ticket_type in types:
            # using a _method() is a bit naughty
            ticket_types_dict[ticket_type] = controller._serialize_ticket_type(ticket_type)

        # create the XML tree
        template_date = datetime.date.today().strftime("%Y-%m-%d")
        self.log.info("Creating ticket type XML file for template archive")

        root = ET.Element("ticket_types", project=self.env.project_name, date=template_date)
        for type_name, type_info in ticket_types_dict.iteritems():
            ET.SubElement(root, "type_name", name=type_name).text = type_info

        # create the xml file
        filename = os.path.join(template_path, 'ticket.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

    def export_workflows(self, req, template_path):
        """Takes all the project specific workflows and copies them into
        into a new template directory. It doesn't matter if these have the same
        name as default workflows, as the project specific workflow has 
        priority."""

        # make a directory to hold workflows
        workflow_template_path = os.path.join(template_path, 'workflows')
        try:
            os.mkdir(workflow_template_path)
        except OSError as exception:
            # if it already exists remove and create it 
            if exception.errno == errno.EEXIST:
                shutil.rmtree(workflow_template_path)
                os.mkdir(workflow_template_path)
        self.log.info("Created a template workflow directory at %s", workflow_template_path)

        # copy the workflows into our new directory
        workflow_dir = os.path.join(self.env.path, 'workflows')
        try:
            for workflow in os.listdir(workflow_dir):
                if workflow.lower().endswith('.xml'):
                    full_file_name = os.path.join(workflow_dir, workflow)
                    if (os.path.isfile(full_file_name)):
                        shutil.copy(full_file_name, workflow_template_path)
                        self.log.info("%s moved to %s template directory", (workflow, workflow_template_path))
        except OSError as exception:
            if exception.errno == errno.ENOENT:
                self.log.debug("No workflows to export from current project.")

    def export_priorites(self, template_path):
        """Get the different ticket priority values from the enum table and
        save the result into a XML file."""

        # create the XML tree
        self.log.info("Creating priority XML file for template archive")
        template_date = datetime.date.today().strftime("%Y-%m-%d")
        root = ET.Element("ticket_priority", project=self.env.project_name, date=template_date)
        for priority in Priority.select(self.env):
            ET.SubElement(root, "priority_info", name=priority.name, value=str(priority.value))

        # create the xml file
        filename = os.path.join(template_path, 'priority.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

    def export_file_archive(self, req, new_repo_path):
        """For now we only deal with Subversion repositories. We won't support
        the export of GIT repos - but we will come back to solve this 
        issue (probably via GIT clone) in a future release."""

        # os.path.basename(self.env.path) is a workaround to get the project
        # name without any spaces etc
        old_repo_path = os.path.join("vc-repos", "svn", os.path.basename(self.env.path))

        if os.path.exists(old_repo_path):
            # Dump the file archive at the latest version (-rHEAD)
            subprocess.call("svnadmin dump -rHEAD %s | gzip > %s" % (old_repo_path, new_repo_path), cwd=os.getcwd(), shell=True)
            self.log.info("Dumped the file archive at %s into the project template directory", old_repo_path)
        else:
            add_notice(req, "Unable to export the file archive.")

    def export_groups(self, template_path):
        """Puts a list of all internal membership groups into an XML file. 
        We ignore linked groups at the moment."""

        self.log.info("Creating membership group XML file for template archive")
        template_date = datetime.date.today().strftime("%Y-%m-%d")
        groups = [Group(self.env, sid) for sid in Group.groupsBy(self.env)]
        if groups:
            root = ET.Element("membership_group", project=self.env.project_name, date=template_date)
            for group in groups:
                if not group.external_group:
                    ET.SubElement(root, "group_info", name=group.name, sid=group.sid, label=group.label).text = group.description

            exteneral_groups = Group.groupsBy(self.env, only_external_groups=True)
            linked_groups = [i for i in groups if exteneral_groups]
            project_groups = [i for i in groups if not exteneral_groups]

            # create the xml file
            filename = os.path.join(template_path, 'group.xml')
            ET.ElementTree(root).write(filename)
            self.log.info("File %s has been created at %s" % (filename, template_path))

    def export_permissions(self, template_path):
        """Collects all permissions from the permissions table"""

        self.log.info("Creating permissions XML file for template archive")
        template_date = datetime.date.today().strftime("%Y-%m-%d")
        root = ET.Element("permissions", project=self.env.project_name, date=template_date)
        for perm in DefaultPermissionStore(self.env).get_all_permissions():
            ET.SubElement(root, "permission", name=perm[0], action=perm[1])

        # create the xml file
        filename = os.path.join(template_path, 'permission.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

        # need to think about permissions and inheritence

    def export_lists(self, template_path):
        """Exports project mailing lists"""

        self.log.info("Creating mailing list XML file for template archive")
        template_date = datetime.date.today().strftime("%Y-%m-%d")
        root = ET.Element("lists", project=self.env.project_name, date=template_date)
        for ml in Mailinglist.select(self.env):
            ET.SubElement(root, "list_info", name=ml.name,
                                             email=ml.emailaddress,
                                             private=str(ml.private),
                                             postperm=ml.postperm,
                                             replyto=ml.replyto).text = ml.description

        # save the xml file
        filename = os.path.join(template_path, 'list.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

    def export_milestones(self, template_path):
        """Exports all project milestones into an XML file, respecting
        the new sub-milestone feature."""

        self.env.log.info("Creating milestone XML file for template archive")
        template_date = datetime.date.today().strftime("%Y-%m-%d")
        root = ET.Element("milestones", project=self.env.project_name, date=template_date)
        all_milestones = Milestone.select(self.env, include_children=True)
        for milestone in all_milestones:
            ms = ET.SubElement(root, "milestone_info", name=milestone.name)
            # we need to do some checking incase the attribute has a None type
            if milestone.start:
                ms.attrib['start'] = milestone.start.strftime("%Y-%m-%d")
            if milestone.due:
                ms.attrib['due'] = milestone.due.strftime("%Y-%m-%d")
            if milestone.completed:
                ms.attrib['completed'] = milestone.completed.strftime("%Y-%m-%d")
            if milestone.parent:
                ms.attrib['parent'] = milestone.parent
            if milestone.description:
                ms.text = milestone.description

        # save the xml file in the template directory
        filename = os.path.join(template_path, 'milestone.xml')
        ET.ElementTree(root).write(filename)
        self.log.info("File %s has been created at %s" % (filename, template_path))

    def create_template_info_file(self, req, template_path):
        """We can store some extra metadata about the template creation - such 
        as the author and date."""

        filename = os.path.join(template_path, "info.txt")
        try:
            f = file(filename, "w")
            time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text = "Created - %s.\nAuthor - %s.\nDescription - %s" \
                    % (time, req.authname, req.args['description'])
            f.write(text)
        except IOError:
            self.log.info("Unable to create new file info folder at %s", filename)

    # ITemplateProvider methods

    def get_htdocs_dirs(self):
        return [('createtemplate', pkg_resources.resource_filename(__name__,
                                                                'htdocs'))]

    def get_templates_dirs(self):
        return [pkg_resources.resource_filename(__name__, 'templates')]