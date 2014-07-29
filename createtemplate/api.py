import json
import os
import itertools

from trac.core import *
from tracrpc.api import IXMLRPCHandler
from trac.config import PathOption
from trac.resource import ResourceNotFound

# Author: Danny Milsom <danny.milsom@cgi.com>

class ProjectTemplatesRPC(Component):
    """ Get information about the project templates available """
    implements(IXMLRPCHandler)

    # IXMLRPCHandler methods
    def xmlrpc_namespace(self):
        return 'project_templates'

    def xmlrpc_methods(self):
        yield (None, ((list,),), self.getTemplatesNames)
        yield (None, ((dict, str),), self.getTemplateInformation)

    def getTemplatesNames(self, req):
        """Get a list of all project templates available."""

        return ProjectTemplateAPI(self.env).get_all_templates()

    def getTemplateInformation(self, req, template_name):
        """Gets information about a specific project template. This includes 
        the date it was created and the description from the info file, as well 
        as a list of all the components exported. Returns a dictionary."""

        return ProjectTemplateAPI(self.env).get_template_information(template_name)

class ProjectTemplateAPI(Component):
    """Useful methods to return information about project templates"""

    template_dir_path = PathOption('project_templates', 'template_dir', 
                    doc="The default path for the project template directory")

    def get_all_templates(self):
        """Gets a list of all templates stored in var/define/templates on 
        production servers or development-environment/templates under
        run-in-place.sh"""

        try:
            # list all directories in the template dir
            return os.walk(self.template_dir_path).next()[1]
        except TypeError:
            # catch a TypeError incase the template_dir_path defaults to None
            return []

    def get_template_information(self, template_name):
        """Returns a dictionary containing information about the specified 
        project template. This includes the name, description, date and a list
        of all the components exported - loaded from the JSON in info.json

        If there is not template directory with that name in the 
        template folder we return a string informing the user. There is 
        no point in returning a warning or notice as this method is intended
        for API style usage."""

        # create the path to the template and check it exists
        template_dir = os.path.join(self.template_dir_path, template_name)
        template_info = {}

        if os.path.isdir(template_dir):
            try:
                with open(os.path.join(template_dir, 'info.json')) as info_file:
                    try:
                        template_info = json.load(info_file)
                    except ValueError:
                        # to catch invalid json syntax
                        self.log.debug("Unable to load JSON from info.json")
            except IOError:
                self.log.debug("Unable to find info.json file in %s", template_dir)

            # get a list of all the files and folders inside the template directory
            # [1] is directories, [2] is files
            template_contents = os.walk(template_dir).next()[1:]
            available_components = list(itertools.chain(*template_contents))

            # add component info into the dict
            # we are only interested in xml files and directories
            template_info['components'] = [template_file.rstrip(".xml") for template_file in available_components 
                                                         if template_file.lower().endswith(".xml") 
                                                         or os.path.isdir(os.path.join(template_dir, template_file))]

            return template_info

        else:
            # no directrory at the path specified
            raise ResourceNotFound('There is no such template with the name %s'
                                   % template_name)